package org.twak.tweed;

import javax.swing.JComponent;
import javax.swing.JPanel;

import org.twak.tweed.gen.Gen;

import com.jme3.collision.CollisionResult;
import com.jme3.collision.CollisionResults;
import com.jme3.math.Vector3f;
import com.jme3.scene.Geometry;
import com.jme3.scene.Mesh;
import com.jme3.scene.Node;

/** Headless regression for generated meshes occluding an on-demand GIS click. */
public final class TweedClickSelectionValidation {

	private TweedClickSelectionValidation() {}

	public static void main( String[] args ) {
		Gen gis = new Gen( "test gis", null ) {
			@Override public JComponent getUI() { return new JPanel(); }
		};

		Geometry generatedMesh = geometry( "generated mesh" );
		Geometry gisFootprint = geometry( "gis footprint" );
		Node gisOwner = new Node( "gis owner" );
		gisOwner.setUserData( Gen.class.getSimpleName(), new Object[] { gis } );
		gisOwner.attachChild( gisFootprint );

		CollisionResults collisions = new CollisionResults();
		collisions.addCollision( new CollisionResult( generatedMesh, Vector3f.ZERO, 1f, 0 ) );
		collisions.addCollision( new CollisionResult( gisFootprint, Vector3f.ZERO, 3f, 0 ) );

		require( Tweed.chooseClickedCollision( collisions, null ).getGeometry() == generatedMesh,
				"legacy picking must keep the closest generated mesh" );
		require( Tweed.chooseClickedCollision( collisions, gis ).getGeometry() == gisFootprint,
				"on-demand GIS picking must pass through the generated mesh" );

		System.out.println( "Tweed on-demand GIS click-through validation passed" );
	}

	private static Geometry geometry( String name ) {
		Mesh mesh = new Mesh();
		mesh.setMode( Mesh.Mode.Triangles );
		return new Geometry( name, mesh );
	}

	private static void require( boolean condition, String message ) {
		if ( !condition )
			throw new IllegalStateException( message );
	}
}
